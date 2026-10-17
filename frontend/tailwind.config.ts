import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#09090b",
        panel: "#17181d",
        panel2: "#212329",
        line: "#2a2d35",
        muted: "#8e94a3",
        text: "#eceef3",
        // Korean market convention: rises red, falls blue. Always paired with ▲/▼ and a sign.
        up: "#f04452",
        down: "#3d8bfd",
        accent: "#f59e0b",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
