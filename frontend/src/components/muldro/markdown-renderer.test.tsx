import { render } from "@testing-library/react";
import { test, expect } from "vitest";
import { InlineMarkdown, MarkdownRenderer } from "./markdown-renderer";

// Inline markdown is used for short strings in heading and caption positions —
// alert titles, A2UI captions, insight summaries — several of them derived from
// external text. A live `target="_blank"` link there puts an attacker's URL in
// muldro's voice with no sender attributed, so `a` is refused outright.
// Block prose keeps its links; the pair below pins both halves.

test("InlineMarkdown renders a markdown link as text, with no anchor", () => {
  const { container } = render(
    <InlineMarkdown content="[Verify your account](https://phish.example)" />,
  );

  expect(container.querySelector("a")).toBeNull();
  expect(container.textContent).toContain("Verify your account");
});

test("InlineMarkdown does not autolink a bare www URL", () => {
  // remarkGfm is applied here, so bare `www.…` and bare email addresses are
  // link candidates too — the vector is wider than `[text](url)`.
  const { container } = render(<InlineMarkdown content="pay at www.phish.example now" />);

  expect(container.querySelector("a")).toBeNull();
  expect(container.textContent).toContain("www.phish.example");
});

test("InlineMarkdown does not autolink a bare email address", () => {
  const { container } = render(<InlineMarkdown content="reply to ops@phish.example" />);

  expect(container.querySelector("a")).toBeNull();
});

test("MarkdownRenderer still renders block prose links (the control)", () => {
  // Proves the assertions above are not passing because the harness cannot see
  // anchors: the same markdown through the block renderer does produce one.
  const { container } = render(<MarkdownRenderer content="[docs](https://example.com)" />);

  const anchor = container.querySelector("a");
  expect(anchor).not.toBeNull();
  expect(anchor?.getAttribute("href")).toBe("https://example.com");
});

// An image is sharper than a link: it needs no click. A tracking pixel
// laundered into an alert title or an insight summary fires a remote fetch on
// render — the founder's IP plus a read receipt confirming the address is live
// and actively monitored.

test("InlineMarkdown renders no <img> for a bare image tag", () => {
  const { container } = render(
    <InlineMarkdown content="![](https://tracker.example/x.gif)" />,
  );

  expect(container.querySelector("img")).toBeNull();
});

test("InlineMarkdown renders no <img> for an image with alt text", () => {
  const { container } = render(
    <InlineMarkdown content="![alt](https://tracker.example/p.png)" />,
  );

  expect(container.querySelector("img")).toBeNull();
});

test("MarkdownRenderer still renders images (the control)", () => {
  // Proves the assertions above are not passing because the harness cannot see
  // images: the same markdown through the block renderer does produce one.
  // NOT an endorsement of that behaviour — see the img note in the renderer.
  const { container } = render(<MarkdownRenderer content="![alt](https://example.com/p.png)" />);

  expect(container.querySelector("img")).not.toBeNull();
});
