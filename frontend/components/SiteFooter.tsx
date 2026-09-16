import Link from "next/link";

/**
 * Required on every page per the product's non-affiliation commitment: this platform
 * is independently built and must never be mistaken for an Anthropic property.
 */
export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-[var(--color-edge)] pt-6 pb-2 text-xs leading-relaxed text-[var(--color-muted)]">
      <p>
        ClaudeCertMastery is an independent preparation platform. It is not affiliated
        with, endorsed by, or sponsored by Anthropic. Claude and related marks belong to
        their respective owners.
      </p>
      <p className="mt-2">
        <Link href="/methodology" className="underline hover:text-[var(--color-accent)]">
          How the practice score works
        </Link>
      </p>
    </footer>
  );
}
