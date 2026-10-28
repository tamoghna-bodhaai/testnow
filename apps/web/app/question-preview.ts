/**
 * A deliberately plain-text summary for dense question queues. Rendering math
 * and figures is reserved for the selected question, where they have room.
 */
export const compactQuestionPreview = (value: string) => value
  .replace(/!\[[^\]]*\]\([^)]*\)/g, "[figure]")
  .replace(/\$\$([\s\S]*?)\$\$/g, "$1")
  .replace(/\$([^$]+)\$/g, "$1")
  .replace(/[`*_#]/g, "")
  .replace(/\s+/g, " ")
  .trim()
  .slice(0, 220);
