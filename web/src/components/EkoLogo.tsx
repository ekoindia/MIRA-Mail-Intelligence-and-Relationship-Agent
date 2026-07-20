/**
 * Eko wordmark: "ek" in bold rounded type + a sun-ring standing in for the
 * second "o" (rays radiating over an open ring), matching the brand mark.
 */
export default function EkoLogo({ className = "h-8 w-auto" }: { className?: string }) {
  // "o" is bottom-aligned to the same baseline as "e"/"k", sized smaller
  // and more compact to match the reference mark — it was too big and
  // hanging too low before.
  const baseline = 90;
  const cx = 114;
  const ringR = 15;
  const cy = baseline - ringR;
  const rayInner = 18;
  const rayOuter = 27;

  const rayAngles = [25, 41.25, 57.5, 73.75, 90, 106.25, 122.5, 138.75, 155];
  const toPoint = (angleDeg: number, r: number) => {
    const rad = (angleDeg * Math.PI) / 180;
    return [cx + r * Math.cos(rad), cy - r * Math.sin(rad)] as const;
  };

  const circumference = 2 * Math.PI * ringR;
  const gapLength = circumference * 0.3;
  const visibleLength = circumference - gapLength;

  return (
    <svg viewBox="0 0 148 115" className={className} xmlns="http://www.w3.org/2000/svg" role="img" aria-label="eko">
      <text
        x="0"
        y={baseline}
        fontFamily="'Poppins', 'Nunito', 'Segoe UI', sans-serif"
        fontWeight="800"
        fontSize="78"
        fill="#F5A524"
        letterSpacing="-2"
      >
        ek
      </text>

      <g stroke="#F5A524" strokeLinecap="round" fill="none">
        <circle
          cx={cx}
          cy={cy}
          r={ringR}
          strokeWidth={8}
          transform={`rotate(-90 ${cx} ${cy})`}
          strokeDasharray={`${visibleLength} ${gapLength}`}
          strokeDashoffset={-gapLength / 2}
        />
        {rayAngles.map((angle) => {
          const [x1, y1] = toPoint(angle, rayInner);
          const [x2, y2] = toPoint(angle, rayOuter);
          return <line key={angle} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={4} />;
        })}
      </g>
    </svg>
  );
}
