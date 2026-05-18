import type { Metadata } from "next";

import { Workspace } from "@/components/Workspace";

export const metadata: Metadata = {
  title: "Workbench | Edna Search",
  description: "Upload, route, review, and export cited research rows.",
};

export default function WorkbenchPage() {
  return <Workspace />;
}
