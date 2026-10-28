import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

/** Prepare the server's bounded excerpt for inline rendering. */
export function compactQuestionPreview(value: string) {
  const normalized = value
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "[figure]")
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `$${math.trim()}$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, math) => `$${math.trim()}$`)
    .replace(/\$\$([\s\S]*?)\$\$/g, (_, math) => `$${math.trim()}$`)
    .replace(/\s+/g, " ")
    .trim();
  // The server may cut an excerpt in the middle of a math expression.
  const lastDollar = normalized.lastIndexOf("$");
  if ((normalized.match(/(?<!\\)\$/g) || []).length % 2) return normalized.slice(0, lastDollar).trimEnd() + "…";
  const incompleteDelimiter = normalized.search(/\\[([]/);
  return incompleteDelimiter < 0 ? normalized : normalized.slice(0, incompleteDelimiter).trimEnd() + "…";
}

export function QuestionPreview({value}: {value: string}) {
  return <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]} components={{p: ({children}) => <span>{children}</span>}}>{compactQuestionPreview(value)}</ReactMarkdown>;
}
