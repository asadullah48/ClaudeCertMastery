import Link from "next/link";
import { Show, SignInButton, SignUpButton, UserButton } from "@clerk/nextjs";

/** Signed-in / signed-out state on every page. Sign out lives in the UserButton menu. */
export function SiteHeader() {
  return (
    <header className="mb-10 flex items-center justify-between gap-4 border-b border-[var(--color-edge)] pb-4">
      <Link href="/" className="text-sm font-semibold tracking-tight hover:text-[var(--color-accent)]">
        Claude Cert Mastery
      </Link>
      <nav className="flex items-center gap-3 text-sm">
        <Show when="signed-out">
          <SignInButton mode="modal">
            <button className="rounded-md px-3 py-1.5 text-[var(--color-muted)] hover:text-[var(--color-accent)]">
              Sign in
            </button>
          </SignInButton>
          <SignUpButton mode="modal">
            <button className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 font-medium text-[var(--color-ink)]">
              Create account
            </button>
          </SignUpButton>
        </Show>
        <Show when="signed-in">
          <UserButton />
        </Show>
      </nav>
    </header>
  );
}
