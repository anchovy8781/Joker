import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0e13",
        panel: "#121722",
        panel2: "#171d2b",
        line: "#232b3b",
        muted: "#8b95a7",
        text: "#e6eaf2",
        up: "#22c55e", // always paired with ▲ and a +sign, never colour alone
        down: "#ef4444", // always paired with ▼ and a −sign
        accent: "#f59e0b",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
