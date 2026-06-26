import type { Config } from "tailwindcss";

export default {
  darkMode: "media",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--color-border))",
        background: "hsl(var(--color-background))",
        foreground: "hsl(var(--color-foreground))",
        muted: "hsl(var(--color-muted))",
        panel: "hsl(var(--color-panel))",
        primary: "hsl(var(--color-primary))",
      },
      borderRadius: {
        lg: "8px",
      },
    },
  },
  plugins: [],
} satisfies Config;
