import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Claude Cert Mastery",
  description:
    "Blueprint-weighted practice exams and scaled scoring for the Claude certification tracks.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <div className="mx-auto max-w-5xl px-6 py-10">{children}</div>
      </body>
    </html>
  );
}
