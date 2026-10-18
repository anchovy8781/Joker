import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Toss-style dark greys: flat surfaces separated by tone, not borders.
        bg: "#17171c",
        panel: "#202027",
        panel2: "#2c2c35",
        line: "#2c2c35",
        muted: "#8b95a1",
        text: "#f2f4f6",
        primary: "#3182f6",
        // Korean market convention: rises red, falls blue. Always paired with ▲/▼ and a sign.
        up: "#f04452",
        down: "#3182f6",
        accent: "#f59e0b",
      },
      fontFamily: {
        sans: ["Pretendard Variable", "Pretendard", "-apple-system", "BlinkMacSystemFont", "system-ui", "Apple SD Gothic Neo", "Noto Sans KR", "sans-serif"],
        // Numbers use the same face with tabular figures (see globals.css) instead of a code font.
        mono: ["Pretendard Variable", "Pretendard", "-apple-system", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
