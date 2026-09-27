/** Same LABEL|body split the PowerPoint renderer uses. The canvas must not keep a second copy of slide wording. */
const GENERIC = new Set(["TITLE", "EXPOSURE", "CARD", "BENEFIT", "ITEM", "POINT", "LABEL"]);
const PREFIXES = ["assumption:", "why this policy:", "watch-out:"];

export function splitBullet(text: string): { label: string; body: string } {
  let raw = text.trim();
  for (const prefix of PREFIXES) {
    if (raw.toLowerCase().startsWith(prefix)) raw = raw.slice(prefix.length).trim();
  }
  let label = "";
  let body = raw;
  const pipe = raw.indexOf("|");
  if (pipe >= 0) {
    label = raw.slice(0, pipe).trim();
    body = raw.slice(pipe + 1).trim();
  } else {
    const colon = raw.indexOf(":");
    if (colon > 0 && colon <= 36) {
      label = raw.slice(0, colon).trim();
      body = raw.slice(colon + 1).trim();
    }
  }
  if (!label || GENERIC.has(label.toUpperCase()) || body.toLowerCase() === label.toLowerCase()) return { label: body, body: "" };
  return { label, body };
}

/** Text the canvas and the deck both take from the shared slide object. */
export function slideLines(slide: { title: string; subtitle?: string | null; bullets: { text: string }[] }): string[] {
  const lines = [slide.title];
  if (slide.subtitle) lines.push(slide.subtitle);
  for (const bullet of slide.bullets) {
    const parts = splitBullet(bullet.text);
    if (parts.label) lines.push(parts.label);
    if (parts.body) lines.push(parts.body);
  }
  return lines;
}
