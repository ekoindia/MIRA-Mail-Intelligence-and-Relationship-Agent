export default function EkoLogo({ className = "h-8 w-auto" }: { className?: string }) {
  // import.meta.env.BASE_URL carries vite.config.ts's `base` — "/" in dev,
  // "/mira/" in the production build — so this resolves correctly under
  // either. A hardcoded "/eko-kiosk-logo.png" 404s once served from a
  // subpath, since nginx only serves this app under /mira/.
  return <img src={`${import.meta.env.BASE_URL}eko-kiosk-logo.png`} alt="Eko Kiosk" className={className} />;
}
