import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import "./globals.css";
import { DesktopOnly } from "@/components/desktop-only";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"], display: "swap", axes: ["opsz"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"], display: "swap" });

export const metadata: Metadata = {
  title: "Marsh Health Policy Advisory",
  description: "Evidence-led health insurance comparison and advisory system.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Dark only; the `dark` class keeps shadcn's dark: variants in force.
  // Desktop only: below `lg` the app is swapped for the DesktopOnly notice (CSS media query, no JS).
  return (
    <html lang="en" className={`${inter.variable} ${geistMono.variable} dark h-full`}>
      <body className="flex min-h-full flex-col">
        <DesktopOnly />
        <div className="hidden min-h-full flex-1 flex-col lg:flex">{children}</div>
      </body>
    </html>
  );
}
