import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import "./desk.css";

export const metadata: Metadata = {
  title: "Edna Search",
  description: "Smart order routing for private-capital research.",
  icons: {
    icon: "/icon.svg",
  },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
