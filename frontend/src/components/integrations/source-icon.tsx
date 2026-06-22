import {
  GoogleLogo,
  GitHubLogo,
  SlackLogo,
  NotionLogo,
  AtlassianLogo,
} from "./logos";

interface SourceIconProps {
  source: string;
  className?: string;
}

// Inline stroke-SVG glyph for non-brand signal types (calendar).
function CalendarGlyph({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="2" y="3" width="12" height="11" rx="1.5" />
      <path d="M2 6.5h12M5 1.5v3M11 1.5v3" />
    </svg>
  );
}

// Generic fallback glyph (bell) for unmapped sources.
function BellGlyph({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M8 2v1M4 7a4 4 0 0 1 8 0c0 3 1 4 1 4H3s1-1 1-4z" />
      <path d="M6.5 13.5a1.6 1.6 0 0 0 3 0" />
    </svg>
  );
}

/**
 * Maps a perception source / signal slug to an inline SVG icon.
 *
 * Brand sources reuse the integration brand logos; non-brand signal types
 * (calendar, generic) use inline stroke SVG glyphs. NEVER emoji.
 */
export function SourceIcon({ source, className = "w-3.5 h-3.5" }: SourceIconProps) {
  const slug = source.toLowerCase();

  switch (slug) {
    case "gmail":
    case "google":
    case "drive":
    case "google_drive":
    case "gdrive":
      return <GoogleLogo className={className} />;
    case "github":
      return <GitHubLogo className={className} />;
    case "slack":
      return <SlackLogo className={className} />;
    case "notion":
      return <NotionLogo className={className} />;
    case "atlassian":
    case "jira":
    case "confluence":
      return <AtlassianLogo className={className} />;
    case "calendar":
    case "google_calendar":
    case "gcal":
      return <CalendarGlyph className={className} />;
    default:
      return <BellGlyph className={className} />;
  }
}
