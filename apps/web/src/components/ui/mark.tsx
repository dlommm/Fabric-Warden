/** The mark: a drawn frame holding the F, with the accent node where a link would land. Fixed colours: it sits on the rail. */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 26 26" className={className} aria-hidden>
      <rect x="0.75" y="0.75" width="24.5" height="24.5" rx="3" fill="none" stroke="#cfe3ff" strokeWidth="1.5" />
      <path d="M7 19V7h10M7 13h7" fill="none" stroke="#cfe3ff" strokeWidth="2" />
      <circle cx="18.5" cy="18.5" r="2.5" fill="#7fc8ff" />
    </svg>
  );
}
