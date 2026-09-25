import Image from "next/image";

/**
 * Viewport gate. The workspace is a three-pane desktop layout (rail, thread, deck) and is not
 * built for phones or portrait tablets, so below `lg` (1024px) the app is replaced by this notice.
 * Pure CSS (`lg:hidden` / `hidden lg:flex` in the root layout): server-rendered, no flash, no JS.
 */
export function DesktopOnly() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-6 bg-canvas px-8 text-center lg:hidden" aria-label="Desktop required">
      <Image src="/marsh-symbol-white.png" alt="Marsh McLennan" width={1024} height={608} priority className="h-6 w-auto" />
      <div className="space-y-2">
        <h1 className="text-lg font-medium tracking-title text-ink">Open this on a desktop</h1>
        <p className="max-w-xs text-sm leading-relaxed text-body">Marsh Advisory works side by side with a live slide deck and needs a wide screen. Open this link on a laptop or desktop browser at least 1024&nbsp;px wide.</p>
      </div>
      <p className="text-xs text-quiet">Mobile and tablet portrait are not supported.</p>
    </main>
  );
}
