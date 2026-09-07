export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 36 36"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className="shrink-0"
    >
      {/* Каскад модульных блоков убывающего размера — асимметричный фрагментированный
          знак с воздухом между частями, читается как разомкнутая "O" (ORACLE). */}
      <rect x="12" y="3" width="15" height="13" rx="4" fill="url(#oracle-logo-gradient)" />
      <rect
        x="4"
        y="12"
        width="9"
        height="9"
        rx="3"
        fill="url(#oracle-logo-gradient)"
        fillOpacity="0.9"
      />
      <rect
        x="14"
        y="17"
        width="6.5"
        height="6.5"
        rx="2.4"
        fill="url(#oracle-logo-gradient)"
        fillOpacity="0.75"
      />
      <rect x="5" y="23" width="9.5" height="9.5" rx="3" fill="url(#oracle-logo-gradient)" />
      <rect
        x="17.5"
        y="25.5"
        width="6.5"
        height="6.5"
        rx="2.4"
        fill="url(#oracle-logo-gradient)"
        fillOpacity="0.85"
      />
      <defs>
        <linearGradient
          id="oracle-logo-gradient"
          x1="4"
          y1="3"
          x2="27"
          y2="32"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#a5b4fc" />
          <stop offset="1" stopColor="#6d28d9" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function Logo({
  size = 30,
  withWordmark = true,
}: {
  size?: number;
  withWordmark?: boolean;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark size={size} />
      {withWordmark && (
        <span className="flex items-baseline gap-1 leading-none tracking-tight">
          <span className="font-bold text-white" style={{ fontSize: size * 0.52 }}>
            ORACLE
          </span>
          <span className="font-semibold text-indigo-400" style={{ fontSize: size * 0.36 }}>
            (T)
          </span>
        </span>
      )}
    </div>
  );
}
