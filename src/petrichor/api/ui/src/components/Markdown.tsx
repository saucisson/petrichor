import type { ReactNode } from "react";

/* Minimal sanitized Markdown renderer (mission §2.7): headings, unordered
   lists, paragraphs, bold and inline code. It builds React elements only -
   never dangerouslySetInnerHTML - so raw HTML in the source is escaped by
   React and can never be injected. The contract Markdown is server data:
   rendered as-is, never translated. */

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      nodes.push(<code key={keyPrefix + "-" + key++}>{token.slice(1, -1)}</code>);
    } else {
      nodes.push(<strong key={keyPrefix + "-" + key++}>{token.slice(2, -2)}</strong>);
    }
    lastIndex = match.index + token.length;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

export default function Markdown({ source }: { source: string }) {
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];
  let blockKey = 0;
  const flushList = () => {
    if (listItems.length > 0) {
      blocks.push(
        <ul key={"ul-" + blockKey++}>
          {listItems.map((item, index) => (
            <li key={index}>{renderInline(item, "li-" + blockKey + "-" + index)}</li>
          ))}
        </ul>,
      );
      listItems = [];
    }
  };
  for (const line of source.split("\n")) {
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    if (heading !== null) {
      flushList();
      const level = Math.min(3 + heading[1].length - 1, 6);
      blocks.push(
        <MarkdownHeading key={"h-" + blockKey++} level={level}>
          {renderInline(heading[2], "h-" + blockKey)}
        </MarkdownHeading>,
      );
    } else if (bullet !== null) {
      listItems.push(bullet[1]);
    } else if (line.trim() === "") {
      flushList();
    } else {
      flushList();
      blocks.push(<p key={"p-" + blockKey++}>{renderInline(line, "p-" + blockKey)}</p>);
    }
  }
  flushList();
  return <div className="markdown-body">{blocks}</div>;
}

function MarkdownHeading({ level, children }: { level: number; children: ReactNode }) {
  const Tag = ("h" + level) as "h3" | "h4" | "h5" | "h6";
  return <Tag>{children}</Tag>;
}
