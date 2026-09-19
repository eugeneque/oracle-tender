import { BrainCircuit, Camera, Lock, Save, Trash2, UserCircle2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError, api, putForm } from "../api/client";
import type { User } from "../api/types";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { AiProviderSwitcher } from "../components/settings/AiProviderControls";
import { UserAvatar } from "../components/UserAvatar";
import { useAuth } from "../context/useAuth";

// Сторона квадрата, до которого картинка ужимается в браузере до отправки. Аватар
// показывается кружком не больше 96 px, и слать исходную фотографию на сервер незачем —
// после ужатия файл весит десятки килобайт и укладывается в серверный предел 512 КБ.
const AVATAR_SIDE = 256;

const ROLE_LABELS: Record<string, string> = {
  admin: "Администратор",
  user: "Пользователь",
};

/**
 * Ужимает картинку до квадрата `AVATAR_SIDE` по центру (обрезка по короткой стороне) и
 * отдаёт JPEG. Через canvas, а не серверной библиотекой: на сервере нет Pillow, а
 * браузер и так умеет.
 */
async function squareAvatar(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file);
  try {
    const side = Math.min(bitmap.width, bitmap.height);
    const sx = (bitmap.width - side) / 2;
    const sy = (bitmap.height - side) / 2;
    const canvas = document.createElement("canvas");
    canvas.width = AVATAR_SIDE;
    canvas.height = AVATAR_SIDE;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Не удалось подготовить изображение");
    ctx.drawImage(bitmap, sx, sy, side, side, 0, 0, AVATAR_SIDE, AVATAR_SIDE);
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error("Не удалось сжать изображение"))),
        "image/jpeg",
        0.88,
      );
    });
  } finally {
    bitmap.close();
  }
}

/**
 * Настройки своей учётной записи (замечание 17.09.2026): имя и аватар. Логин и пароль
 * здесь показаны, но не редактируются — их меняет администратор в «Пользователях»; поле
 * с замком объясняет это на месте, чтобы не искали кнопку.
 */
export function AccountPage() {
  const { user, updateUser } = useAuth();
  const fileInput = useRef<HTMLInputElement>(null);
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setFullName(user?.full_name ?? "");
  }, [user?.full_name]);

  const isDirty = fullName.trim() !== "" && fullName.trim() !== user?.full_name;

  const saveName = async () => {
    if (!isDirty) return;
    setIsSaving(true);
    setError(null);
    setNotice(null);
    try {
      updateUser(await api.patch<User>("/auth/me", { full_name: fullName.trim() }));
      setNotice("Имя сохранено");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить имя");
    } finally {
      setIsSaving(false);
    }
  };

  const uploadAvatar = async (file: File) => {
    setIsUploading(true);
    setError(null);
    setNotice(null);
    try {
      if (!file.type.startsWith("image/")) throw new Error("Выберите картинку — JPEG, PNG или WebP");
      const blob = await squareAvatar(file);
      const form = new FormData();
      form.append("file", blob, "avatar.jpg");
      updateUser(await putForm<User>("/auth/me/avatar", form));
      setNotice("Аватар обновлён");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить аватар");
    } finally {
      setIsUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const removeAvatar = async () => {
    setIsUploading(true);
    setError(null);
    setNotice(null);
    try {
      updateUser(await api.delete<User>("/auth/me/avatar"));
      setNotice("Аватар удалён");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить аватар");
    } finally {
      setIsUploading(false);
    }
  };

  const lockedField = (label: string, value: string) => (
    <div>
      <label className="mb-1 flex items-center gap-1.5 text-xs text-zinc-500">
        {label}
        <Lock size={11} className="text-zinc-600" />
      </label>
      <div
        className="flex items-center justify-between rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-sm text-zinc-400"
        title="Меняет администратор в разделе «Пользователи»"
      >
        <span>{value}</span>
        <span className="text-[11px] text-zinc-600">только администратор</span>
      </div>
    </div>
  );

  return (
    <AppShell>
      <div className="px-8 pb-10 pt-6">
        <PageHeader
          breadcrumb={["Sova Scanner", "Учётная запись"]}
          title="Учётная запись"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-400">
              <UserCircle2 size={18} />
            </span>
          }
        />

        {error && (
          <div className="mb-5 max-w-2xl rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-5 max-w-2xl rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-4 py-2.5 text-sm text-emerald-400">
            {notice}
          </div>
        )}

        <div className="grid max-w-4xl grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
          {/* Аватар */}
          <section className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6">
            <h2 className="mb-4 text-sm font-semibold text-white">Аватар</h2>
            <div className="flex flex-col items-center gap-4">
              <button
                onClick={() => fileInput.current?.click()}
                disabled={isUploading}
                className="group relative rounded-full ring-1 ring-white/10 transition-shadow hover:shadow-[0_0_0_4px_rgba(99,102,241,0.25)] disabled:opacity-60"
                title="Загрузить новую картинку"
              >
                <UserAvatar user={user} size="xl" />
                <span className="absolute inset-0 flex items-center justify-center rounded-full bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
                  <Camera size={22} className="text-white" />
                </span>
              </button>
              <input
                ref={fileInput}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void uploadAvatar(file);
                }}
              />
              <div className="flex items-center gap-2">
                <button
                  onClick={() => fileInput.current?.click()}
                  disabled={isUploading}
                  className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                >
                  <Camera size={13} />
                  {isUploading ? "Загрузка…" : "Выбрать файл"}
                </button>
                {user?.avatar_updated_at && (
                  <button
                    onClick={() => void removeAvatar()}
                    disabled={isUploading}
                    className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50"
                  >
                    <Trash2 size={13} />
                    Удалить
                  </button>
                )}
              </div>
              <p className="text-center text-[11px] leading-relaxed text-zinc-600">
                JPEG, PNG или WebP. Картинка обрезается до квадрата и уменьшается до{" "}
                {AVATAR_SIDE}&nbsp;px прямо в браузере.
              </p>
            </div>
          </section>

          {/* Профиль */}
          <section className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6">
            <h2 className="mb-4 text-sm font-semibold text-white">Профиль</h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className="mb-1 block text-xs text-zinc-500">Имя</label>
                <input
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void saveName();
                  }}
                  maxLength={255}
                  placeholder="Фамилия Имя Отчество"
                  className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
                />
                <p className="mt-1 text-[11px] text-zinc-600">
                  Так вас видят в истории закупок, уведомлениях и списке ответственных.
                </p>
              </div>
              {lockedField("Логин", user?.username ?? "")}
              {lockedField("Пароль", "••••••••")}
              <div>
                <label className="mb-1 block text-xs text-zinc-500">Роль</label>
                <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-sm text-zinc-300">
                  {ROLE_LABELS[user?.role ?? ""] ?? user?.role}
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs text-zinc-500">В системе с</label>
                <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-sm text-zinc-300">
                  {user ? new Date(user.created_at).toLocaleDateString("ru-RU") : "—"}
                </div>
              </div>
            </div>

            <div className="mt-5 flex items-center justify-between gap-3 border-t border-white/[0.06] pt-4">
              <p className="text-xs text-zinc-600">
                Сменить логин или пароль может только администратор — обратитесь к нему.
              </p>
              <button
                onClick={() => void saveName()}
                disabled={!isDirty || isSaving}
                className="flex shrink-0 items-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3.5 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
              >
                <Save size={14} />
                {isSaving ? "Сохраняю…" : "Сохранить"}
              </button>
            </div>
          </section>

          {/* Модель ИИ — персональный выбор (18.09.2026): действует на все запросы этого
              пользователя и запущенные им фоновые задачи, других не затрагивает. */}
          <section className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-6 lg:col-span-2">
            <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold text-white">
              <BrainCircuit size={15} className="text-indigo-400" />
              Модель ИИ
            </h2>
            <p className="mb-4 text-xs text-zinc-500">
              Какая модель разбирает для вас закупки: оценку по профилю, требования, разделы
              «Дополнительно», сводку аналитики. Выбор личный — у коллег может быть другая. Сменить
              можно и прямо в карточке тендера, нажав на имя модели в блоке «Разбор ИИ».
            </p>
            <AiProviderSwitcher scope="me" />
          </section>
        </div>
      </div>
    </AppShell>
  );
}
