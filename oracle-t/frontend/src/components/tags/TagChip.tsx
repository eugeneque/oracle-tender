import type { TenderTag } from "../../api/types";
import { tagColorClasses } from "../../utils/tagColors";

export function TagChip({
  tag,
  size = "sm",
  onClick,
  active = true,
  title,
}: {
  tag: TenderTag;
  size?: "xs" | "sm";
  onClick?: () => void;
  /** Погашенный чип — тег есть, но не выбран (в фильтрах и в редакторе). */
  active?: boolean;
  title?: string;
}) {
  const colors = tagColorClasses(tag.color);
  const className = `inline-flex max-w-full items-center gap-1 rounded-full border font-medium leading-none ${
    size === "xs" ? "px-1.5 py-[3px] text-[10px]" : "px-2 py-1 text-[11px]"
  } ${active ? colors.chip : "border-white/10 text-zinc-500"} ${
    onClick ? "cursor-pointer transition-colors hover:brightness-125" : ""
  }`;
  const content = (
    <>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${active ? colors.dot : "bg-zinc-600"}`} />
      <span className="truncate">{tag.name}</span>
    </>
  );
  if (onClick) {
    return (
      <button type="button" onClick={onClick} className={className} title={title ?? tag.name}>
        {content}
      </button>
    );
  }
  return (
    <span className={className} title={title ?? tag.name}>
      {content}
    </span>
  );
}

/** Ряд тегов для карточек списка: не больше `max`, остаток — счётчиком. */
export function TagRow({ tags, max = 3 }: { tags: TenderTag[]; max?: number }) {
  if (tags.length === 0) return null;
  const shown = tags.slice(0, max);
  const rest = tags.length - shown.length;
  return (
    <span className="flex flex-wrap items-center gap-1">
      {shown.map((tag) => (
        <TagChip key={tag.id} tag={tag} size="xs" />
      ))}
      {rest > 0 && (
        <span
          className="rounded-full border border-white/10 px-1.5 py-[3px] text-[10px] leading-none text-zinc-500"
          title={tags.slice(max).map((tag) => tag.name).join(", ")}
        >
          +{rest}
        </span>
      )}
    </span>
  );
}
