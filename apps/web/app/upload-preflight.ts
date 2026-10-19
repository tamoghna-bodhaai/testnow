export type UploadConfig = { extensions: string[]; max_file_bytes: number };
export type UploadFileInfo = { name: string; size: number; lastModified?: number };
export function sourceStem(filename: string) {
  return filename.replace(/\.(pdf|docx)$/i, "").replace(/(answer[_ -]?key|answers?|solutions?)/gi, "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}
export function reviewUploads(questions: UploadFileInfo[], answers: UploadFileInfo[], config: UploadConfig) {
  const seen = new Set<string>();
  return [...questions.map(file => ({ file, role: "question" })), ...answers.map(file => ({ file, role: "answer" }))].map(({ file, role }) => {
    const errors: string[] = [];
    if (!config.extensions.includes(file.name.split(".").pop()?.toLowerCase() || "")) errors.push("Unsupported format");
    if (!file.size) errors.push("Empty file");
    if (file.size > config.max_file_bytes) errors.push("Exceeds the configured file-size limit");
    const identity = `${file.name}:${file.size}:${file.lastModified || ""}`;
    const duplicate = seen.has(identity); seen.add(identity);
    const matches = answers.filter(answer => sourceStem(answer.name) === sourceStem(file.name));
    if (role === "question" && matches.length > 1) errors.push("Multiple matching answer files; select one");
    const paired = role === "question" ? matches[0]?.name || (questions.length === 1 && answers.length === 1 ? answers[0].name : null) : null;
    return { filename: file.name, role, bytes: file.size, errors, duplicate, paired };
  });
}
