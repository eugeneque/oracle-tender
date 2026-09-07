#!/usr/bin/env python3
"""
Транскрибатор видео/аудио с распознаванием говорящих (диаризацией).

Пайплайн:
  1) ffmpeg     — вытаскивает из видео моно-дорожку 16 кГц;
  2) Whisper    — распознаёт речь с таймкодами по каждому слову (faster-whisper);
  3) диаризация — размечает, кто и когда говорит (pyannote или встроенный фолбэк);
  4) склейка    — каждому слову назначается говорящий, слова собираются в реплики.

Результат: .txt (диалог), .srt (субтитры с именами), .json (сырые данные).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path

# Библиотеки шумят предупреждениями об устаревших API и делением на ноль
# на участках тишины — на результат это не влияет, а вывод засоряет.
warnings.filterwarnings("ignore")
logging.getLogger("speechbrain").setLevel(logging.ERROR)
logging.getLogger("pyannote").setLevel(logging.ERROR)
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# Xet-загрузчик Hugging Face умеет намертво зависать на скачивании весов:
# соединения с CDN рвутся, таймаута нет, процесс просто спит. Обычный HTTP
# качает с докачкой и не залипает.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# ---------------------------------------------------------------- структуры


@dataclass
class Word:
    start: float
    end: float
    text: str
    speaker: str = "?"


@dataclass
class Turn:
    """Реплика — подряд идущие слова одного говорящего."""

    speaker: str
    start: float
    end: float
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(w.text for w in self.words).strip()


# ---------------------------------------------------------------- утилиты


def log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


def hhmmss(seconds: float, sep: str = ".") -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def extract_audio(src: Path, dst: Path) -> None:
    """Достаёт из любого видео/аудио дорожку WAV 16 кГц моно — то, что ждут модели."""
    log(f"извлекаю аудио из {src.name} ...")
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-loglevel", "error", str(dst),
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit("Не найден ffmpeg. Установите: brew install ffmpeg")
    except subprocess.CalledProcessError as e:
        sys.exit(f"ffmpeg не смог обработать файл (код {e.returncode}).")


def media_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- 1. Whisper


def transcribe(wav: Path, model_size: str, language: str | None,
               prompt: str | None) -> list[Word]:
    from faster_whisper import WhisperModel

    log(f"загружаю модель Whisper «{model_size}» — при первом запуске "
        f"скачивается, large-v3 весит около 3 ГБ ...")
    model = WhisperModel(
        model_size,
        device="cpu",              # CTranslate2 на Apple Silicon работает на CPU
        compute_type="int8",       # int8 — быстро и почти без потери качества
        cpu_threads=os.cpu_count() or 8,
    )

    log("распознаю речь ...")
    segments, info = model.transcribe(
        str(wav),
        language=language,                 # None -> автоопределение
        task="transcribe",
        beam_size=5,
        best_of=5,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        condition_on_previous_text=False,  # защита от зацикливания на длинных записях
        word_timestamps=True,              # нужны для привязки слов к говорящим
        vad_filter=True,                   # отсекаем тишину -> меньше галлюцинаций
        vad_parameters={"min_silence_duration_ms": 500},
        initial_prompt=prompt,             # подсказка с терминами и именами
        hallucination_silence_threshold=2.0,
    )
    log(f"язык: {info.language} (уверенность {info.language_probability:.0%})")

    total = max(info.duration, 1e-6)
    words: list[Word] = []
    for seg in segments:                   # segments — генератор, считается «на лету»
        for w in seg.words or []:
            if w.word.strip():
                words.append(Word(start=w.start, end=w.end, text=w.word))
        print(f"\r  распознано {hhmmss(seg.end, ',')[:8]} "
              f"({min(seg.end / total, 1):.0%})", end="", file=sys.stderr, flush=True)
    print("", file=sys.stderr)

    if not words:
        sys.exit("Речь не распознана — проверьте, есть ли на дорожке звук.")
    log(f"слов распознано: {len(words)}")
    return words


# ------------------------------------------------- 2а. диаризация: pyannote


def diarize_pyannote(wav: Path, token: str, speakers: int | None,
                     min_speakers: int | None, max_speakers: int | None):
    import torch
    from pyannote.audio import Pipeline

    log("загружаю pyannote speaker-diarization-3.1 ...")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", use_auth_token=token
    )
    if pipeline is None:
        sys.exit(
            "pyannote не отдал модель. Проверьте, что токен верный и что на\n"
            "  huggingface.co/pyannote/speaker-diarization-3.1 и\n"
            "  huggingface.co/pyannote/segmentation-3.0\n"
            "приняты условия использования (кнопка Agree)."
        )
    if torch.backends.mps.is_available():
        pipeline.to(torch.device("mps"))   # на Apple Silicon это заметно быстрее

    kwargs = {}
    if speakers:
        kwargs["num_speakers"] = speakers
    else:
        if min_speakers:
            kwargs["min_speakers"] = min_speakers
        if max_speakers:
            kwargs["max_speakers"] = max_speakers

    log("размечаю говорящих ...")
    annotation = pipeline(str(wav), **kwargs)
    return [(t.start, t.end, label)
            for t, _, label in annotation.itertracks(yield_label=True)]


# --------------------------------------------- 2б. диаризация: без токена


def diarize_simple(wav: Path, words: list[Word], speakers: int | None,
                   max_speakers: int, threshold: float):
    """
    Фолбэк без Hugging Face-токена: режем речь на короткие окна, считаем
    голосовой отпечаток каждого окна (ECAPA) и кластеризуем отпечатки.
    Точность ниже pyannote, особенно при перебивках, но работает «из коробки».
    """
    import numpy as np
    import torch
    import torchaudio
    from speechbrain.inference.speaker import EncoderClassifier

    windows = _speech_windows(words, win=2.0, hop=1.0)
    if len(windows) < 2:
        return [(w[0], w[1], "SPEAKER_00") for w in windows]

    log(f"считаю голосовые отпечатки ({len(windows)} окон) ...")
    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=str(Path.home() / ".cache" / "speechbrain-ecapa"),
        run_opts={"device": "cpu"},
    )
    signal, sr = torchaudio.load(str(wav))
    signal = signal[0]

    embeddings = []
    for start, end in windows:
        chunk = signal[int(start * sr):int(end * sr)]
        with torch.no_grad():
            emb = encoder.encode_batch(chunk.unsqueeze(0)).squeeze().cpu().numpy()
        embeddings.append(emb)
    X = np.vstack(embeddings)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9

    labels = _cluster_voices(X, speakers, max_speakers, threshold)
    labels = _smooth_labels(labels)

    log(f"найдено говорящих: {len(set(labels))}")
    turns = [(s, e, f"SPEAKER_{int(l):02d}") for (s, e), l in zip(windows, labels)]
    return _merge_adjacent(turns)


def _cluster_voices(X, speakers: int | None, max_speakers: int, split_threshold: float):
    """
    Разбивает голосовые отпечатки на кластеры-говорящих.

    linkage="ward" по нормализованным векторам (для них евклидово расстояние
    эквивалентно косинусному). Напрашивающийся average+cosine на длинных
    записях ведёт себя плохо: он отделяет один шумный выброс вместо реальной
    границы между голосами — на 16-минутной встрече давал разбиение 774/1.
    """
    import numpy as np
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    if speakers:
        return AgglomerativeClustering(n_clusters=speakers, linkage="ward").fit_predict(X)

    best, best_score = None, -1.0
    for n in range(2, min(max_speakers, len(X) - 1) + 1):
        labels = AgglomerativeClustering(n_clusters=n, linkage="ward").fit_predict(X)
        score = silhouette_score(X, labels, metric="cosine")
        log(f"вариант «{n} голоса»: чёткость разделения {score:.2f}")
        if score > best_score:
            best, best_score = labels, score

    if best is None or best_score < split_threshold:
        return np.zeros(len(X), dtype=int)   # голоса неразличимы — считаем, что говорит один
    return best


def _smooth_labels(labels):
    """Одиночный чужой ярлык между двумя одинаковыми — почти всегда ошибка окна."""
    out = labels.copy()
    for i in range(1, len(labels) - 1):
        if labels[i - 1] == labels[i + 1] != labels[i]:
            out[i] = labels[i - 1]
    return out


def _speech_windows(words: list[Word], win: float, hop: float):
    """Нарезает участки речи (по словам Whisper) на перекрывающиеся окна."""
    blocks: list[list[float]] = []
    for w in words:
        if blocks and w.start - blocks[-1][1] < 0.4:
            blocks[-1][1] = w.end
        else:
            blocks.append([w.start, w.end])

    windows = []
    for start, end in blocks:
        if end - start < 0.6:
            continue
        if end - start <= win:
            windows.append((start, end))
            continue
        t = start
        while t + win <= end:
            windows.append((t, t + win))
            t += hop
        if end - t > 0.6:
            windows.append((end - win, end))
    return windows


def _merge_adjacent(turns):
    merged = []
    for start, end, spk in sorted(turns):
        if merged and merged[-1][2] == spk and start - merged[-1][1] < 1.0:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), spk)
        else:
            merged.append((start, end, spk))
    return merged


# ---------------------------------------------------------------- 3. склейка


def assign_speakers(words: list[Word], turns) -> None:
    """Каждому слову — говорящий с наибольшим перекрытием по времени."""
    for w in words:
        best, best_overlap = None, 0.0
        for start, end, spk in turns:
            overlap = min(w.end, end) - max(w.start, start)
            if overlap > best_overlap:
                best, best_overlap = spk, overlap
        if best is None:                    # слово попало в «дыру» разметки
            best = min(
                turns,
                key=lambda t: min(abs(t[0] - w.start), abs(t[1] - w.end)),
                default=(0, 0, "SPEAKER_00"),
            )[2]
        w.speaker = best


def group_turns(words: list[Word], max_pause: float = 2.0) -> list[Turn]:
    turns: list[Turn] = []
    for w in words:
        same = turns and turns[-1].speaker == w.speaker
        if same and w.start - turns[-1].end <= max_pause:
            turns[-1].words.append(w)
            turns[-1].end = w.end
        else:
            turns.append(Turn(speaker=w.speaker, start=w.start, end=w.end, words=[w]))
    return [t for t in turns if t.text]


def name_speakers(turns: list[Turn], names: list[str]) -> dict[str, str]:
    """Раздаёт человекочитаемые имена в порядке первого появления."""
    mapping: dict[str, str] = {}
    for t in turns:
        if t.speaker not in mapping:
            i = len(mapping)
            mapping[t.speaker] = names[i] if i < len(names) else f"Спикер {i + 1}"
    return mapping


# ---------------------------------------------------------------- 4. вывод


def write_txt(path: Path, turns: list[Turn], names: dict[str, str]) -> None:
    lines = []
    for t in turns:
        lines.append(f"[{hhmmss(t.start)[:8]}] {names[t.speaker]}: {t.text}")
    path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def write_srt(path: Path, turns: list[Turn], names: dict[str, str],
              max_chars: int = 90) -> None:
    blocks, idx = [], 1
    for t in turns:
        for start, end, text in _split_for_subtitles(t, max_chars):
            blocks.append(
                f"{idx}\n{hhmmss(start, ',')} --> {hhmmss(end, ',')}\n"
                f"{names[t.speaker]}: {text}\n"
            )
            idx += 1
    path.write_text("\n".join(blocks), encoding="utf-8")


def _split_for_subtitles(turn: Turn, max_chars: int):
    cur: list[Word] = []
    for w in turn.words:
        cur.append(w)
        line = "".join(x.text for x in cur).strip()
        if len(line) >= max_chars or line.endswith((".", "!", "?")):
            yield cur[0].start, cur[-1].end, line
            cur = []
    if cur:
        yield cur[0].start, cur[-1].end, "".join(x.text for x in cur).strip()


def write_json(path: Path, turns: list[Turn], names: dict[str, str],
               source: Path) -> None:
    data = {
        "source": str(source),
        "speakers": sorted(set(names.values())),
        "turns": [
            {
                "speaker": names[t.speaker],
                "speaker_id": t.speaker,
                "start": round(t.start, 2),
                "end": round(t.end, 2),
                "text": t.text,
                "words": [
                    {"start": round(w.start, 2), "end": round(w.end, 2),
                     "text": w.text.strip()} for w in t.words
                ],
            }
            for t in turns
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- CLI


def main() -> None:
    p = argparse.ArgumentParser(
        description="Транскрибация видео/аудио с разделением по говорящим.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Пример: python transcribe.py встреча.mp4 --speakers 3",
    )
    p.add_argument("input", type=Path, help="видео или аудио файл")
    p.add_argument("-o", "--outdir", type=Path, default=None,
                   help="куда класть результат (по умолчанию — рядом с файлом)")
    p.add_argument("-m", "--model", default="large-v3",
                   help="модель Whisper: large-v3 (точнее) | medium | small (быстрее)")
    p.add_argument("-l", "--language", default="ru",
                   help="язык записи; auto — определить автоматически")
    p.add_argument("--prompt", default=None,
                   help="подсказка с именами/терминами — повышает точность их написания")
    p.add_argument("-s", "--speakers", type=int, default=None,
                   help="точное число говорящих, если известно")
    p.add_argument("--min-speakers", type=int, default=None)
    p.add_argument("--max-speakers", type=int, default=6)
    p.add_argument("--names", default=None,
                   help="имена говорящих через запятую, в порядке появления")
    p.add_argument("--diarizer", choices=["auto", "pyannote", "simple"], default="auto",
                   help="auto: pyannote при наличии токена, иначе встроенный")
    p.add_argument("--hf-token", default=None,
                   help="токен Hugging Face (или переменная окружения HF_TOKEN)")
    p.add_argument("--split-threshold", type=float, default=0.20, dest="threshold",
                   help="минимальная чёткость разделения голосов (0..1); ниже неё "
                        "считаем, что говорит один человек. Учитывается, только "
                        "когда не задан --speakers")
    p.add_argument("--keep-wav", action="store_true", help="не удалять извлечённое аудио")
    args = p.parse_args()

    src: Path = args.input.expanduser()
    if not src.exists():
        sys.exit(f"Файл не найден: {src}")

    outdir = (args.outdir or src.parent).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    token = args.hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    diarizer = args.diarizer
    if diarizer == "auto":
        diarizer = "pyannote" if token else "simple"
    if diarizer == "pyannote" and not token:
        sys.exit("Для pyannote нужен токен: --hf-token ... или export HF_TOKEN=...")

    dur = media_duration(src)
    log(f"файл: {src.name}" + (f", длительность {hhmmss(dur)[:8]}" if dur else ""))
    log(f"диаризация: {diarizer}")

    tmpdir = Path(tempfile.mkdtemp(prefix="transcriber-"))
    wav = tmpdir / f"{stem}.wav"
    try:
        extract_audio(src, wav)

        language = None if args.language.lower() == "auto" else args.language
        words = transcribe(wav, args.model, language, args.prompt)

        if diarizer == "pyannote":
            turns_raw = diarize_pyannote(wav, token, args.speakers,
                                         args.min_speakers, args.max_speakers)
        else:
            turns_raw = diarize_simple(wav, words, args.speakers,
                                       args.max_speakers, args.threshold)

        assign_speakers(words, turns_raw)
        turns = group_turns(words)
        names = name_speakers(turns, [n.strip() for n in args.names.split(",")]
                              if args.names else [])

        txt, srt, js = (outdir / f"{stem}.txt", outdir / f"{stem}.srt",
                        outdir / f"{stem}.json")
        write_txt(txt, turns, names)
        write_srt(srt, turns, names)
        write_json(js, turns, names, src)

        log(f"говорящих: {len(names)} — {', '.join(names.values())}")
        log(f"реплик: {len(turns)}")
        print(f"\nГотово:\n  {txt}\n  {srt}\n  {js}")
    finally:
        if args.keep_wav:
            log(f"аудио сохранено: {wav}")
        else:
            wav.unlink(missing_ok=True)
            tmpdir.rmdir()


if __name__ == "__main__":
    main()
