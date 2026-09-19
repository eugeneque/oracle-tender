import type { User } from "../api/types";
import { initials, useAvatarUrl } from "../utils/avatar";

// Хук и инициалы вынесены в `utils/avatar.ts`: Fast Refresh в Vite работает только когда
// файл экспортирует одни компоненты.
const SIZE_CLASSES = {
  sm: "h-9 w-9 text-xs",
  md: "h-10 w-10 text-sm",
  lg: "h-14 w-14 text-lg",
  xl: "h-24 w-24 text-2xl",
} as const;

/** Кружок с аватаром или инициалами — один вид во всех местах, где показан человек. */
export function UserAvatar({
  user,
  size = "md",
  className = "",
}: {
  user: Pick<User, "id" | "full_name" | "avatar_updated_at"> | null;
  size?: keyof typeof SIZE_CLASSES;
  className?: string;
}) {
  const url = useAvatarUrl(user);
  const base = `flex shrink-0 items-center justify-center overflow-hidden rounded-full font-semibold text-white ${SIZE_CLASSES[size]} ${className}`;
  if (url) {
    return (
      <span className={`${base} bg-zinc-800`}>
        <img src={url} alt={user?.full_name ?? ""} className="h-full w-full object-cover" />
      </span>
    );
  }
  return (
    <span className={`${base} bg-gradient-to-br from-indigo-500 to-violet-600`}>
      {initials(user?.full_name)}
    </span>
  );
}
