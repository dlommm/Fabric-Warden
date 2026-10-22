/* eslint-disable @typescript-eslint/no-require-imports -- loaded by Next.js with require() at build time */
// Offline stand-in for Google Fonts during the E2E build.
//
// next/font/google downloads font CSS and files at build time. Next.js reads
// NEXT_FONT_GOOGLE_MOCKED_RESPONSES (a module mapping stylesheet URL -> CSS) and,
// when it is set, reads absolute font paths from disk instead of fetching them.
// This answers every stylesheet URL with one @font-face pointing at a local
// placeholder file, so the E2E build never touches the network. Browsers reject
// the placeholder and use the fallback font, which is irrelevant to the flows.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const placeholder = path.join(os.tmpdir(), "fabric-warden-e2e-font.woff2");
if (!fs.existsSync(placeholder)) fs.writeFileSync(placeholder, Buffer.from("wOF2-placeholder"));

function css(url) {
  const family = decodeURIComponent((/family=([^:&]+)/.exec(url) || [, "Fallback"])[1]).replace(/\+/g, " ");
  return `/* latin */
@font-face {
  font-family: '${family}';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url(${placeholder}) format('woff2');
}
`;
}

module.exports = new Proxy({}, { get: (_target, url) => (typeof url === "string" ? css(url) : undefined) });
