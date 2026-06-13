/** Regex: match up to the first sentence-ending punctuation followed by whitespace. */
const SENTENCE_RE = /^([\s\S]*?[.!?])\s+([\s\S]*)$/;

/**
 * Try to extract a complete sentence from an accumulating text buffer.
 * Returns the completed sentence and the remaining text, or null if
 * no sentence boundary has been reached yet.
 */
export function extractSentence(buffer: string): { sentence: string; remainder: string } | null {
  const match = buffer.match(SENTENCE_RE);
  if (!match) return null;
  const sentence = match[1].trim();
  if (!sentence) return null;
  return { sentence, remainder: match[2] };
}

/** Strip markdown formatting and collapse whitespace for clean TTS input. */
export function cleanForTTS(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, 'code block omitted')
    .replace(/[#*_~`>\[\]]/g, '')
    .replace(/\n+/g, ' ')
    .trim();
}
