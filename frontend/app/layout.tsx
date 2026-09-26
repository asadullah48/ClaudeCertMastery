import type { Metadata } from "next";
import { ClerkProvider } from "@clerk/nextjs";
import { dark } from "@clerk/themes";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";
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
        <ClerkProvider appearance={{ theme: dark, variables: { colorPrimary: "#c96442" } }}>
          <div className="mx-auto max-w-5xl px-6 py-10">
            <SiteHeader />
            {children}
            <SiteFooter />
          </div>
        </ClerkProvider>
      </body>
    </html>
  );
}
