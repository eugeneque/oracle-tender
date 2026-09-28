export function Badge({ tone, children }: { tone: "green" | "amber" | "red" | "zinc"; children: React.ReactNode }) {
  const tones = {
    green: "bg-emerald-500/10 text-emerald-400",
    amber: "bg-amber-500/10 text-amber-400",
    red: "bg-red-500/10 text-red-400",
    zinc: "bg-zinc-500/10 text-zinc-400",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}
