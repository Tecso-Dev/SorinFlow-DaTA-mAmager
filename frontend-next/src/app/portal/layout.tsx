import { CircleHelp } from "lucide-react";
import { ConfirmProvider } from "@/components/panel/kit";

// The public portal's own root: no AppShell (that is the staff panel's), just
// the confirm dialog every signed-in portal page needs (deleting a request)
// — mounted once here rather than per page, the same way the panel mounts it
// once in its own shell.
export default function PortalLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <ConfirmProvider fallbackIcon={CircleHelp}>{children}</ConfirmProvider>
    </div>
  );
}
