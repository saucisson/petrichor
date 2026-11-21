import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Busy from "../src/components/Busy";
import Markdown from "../src/components/Markdown";
import { applyLanguage } from "../src/i18n";

/* P79 (issue #188): the shared <Busy> active-wait indicator (spinner +
   elapsed seconds in role="status") and the sanitized Markdown renderer of
   the contract (React elements only - raw HTML is never injected). */

beforeEach(() => {
  applyLanguage("fr");
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  applyLanguage("fr");
});

describe("P79 — <Busy>", () => {
  it("shows the label and the elapsed seconds ticking inside role=status", () => {
    vi.useFakeTimers();
    render(<Busy label="Import du modèle…" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Import du modèle…");
    expect(status).toHaveTextContent("depuis 0 s");
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(status).toHaveTextContent("depuis 3 s");
  });
});

describe("P79 — Markdown assaini", () => {
  it("renders headings, lists, bold and inline code as elements", () => {
    render(<Markdown source={"# Contrat\n\n- clause **AG(x)** avec `code`\n- autre"} />);
    expect(screen.getByRole("heading", { name: "Contrat" })).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("clause AG(x) avec code");
    expect(items[0].querySelector("strong")).toHaveTextContent("AG(x)");
    expect(items[0].querySelector("code")).toHaveTextContent("code");
  });

  it("never injects raw HTML: tags are escaped as literal text", () => {
    const { container } = render(
      <Markdown source={"<script>alert(1)</script>\n\n<img src=x onerror=alert(1)> **gras**"} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("<script>alert(1)</script>");
    expect(container).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(container.querySelector("strong")).toHaveTextContent("gras");
  });
});
