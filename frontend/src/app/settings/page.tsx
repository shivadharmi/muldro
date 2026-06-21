"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useSettingsModalStore } from "@/stores/settings-modal-store";

/**
 * Settings is presented as a popup modal opened from the sidebar (see
 * SettingsModal mounted in AppShell). This route exists only for deep links:
 * it opens the modal and sends the user back to the workspace so the modal
 * renders over a real surface rather than a blank page.
 */
export default function SettingsPage() {
  const openSettings = useSettingsModalStore((s) => s.openSettings);
  const router = useRouter();

  useEffect(() => {
    openSettings();
    router.replace("/");
  }, [openSettings, router]);

  return null;
}
