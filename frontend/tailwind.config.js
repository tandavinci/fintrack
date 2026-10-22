/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Minimal base: deep slate canvas + soft surfaces.
        canvas: "#0b0f1a",
        surface: "#121829",
        "surface-2": "#1a2238",
        border: "#263150",
        muted: "#8b97b8",
        // Colourful, purposeful accents.
        brand: "#6366f1", // indigo — the FinTrack accent
        buy: "#10b981", // emerald green
        sell: "#f43f5e", // rose red
        hold: "#f59e0b", // amber
        bull: "#22d3ee", // cyan
        bear: "#fb7185", // soft red
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(99,102,241,0.25), 0 8px 30px rgba(2,6,23,0.6)",
        card: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 10px 30px -12px rgba(0,0,0,0.6)",
      },
      borderRadius: {
        xl2: "1.25rem",
      },
    },
  },
  plugins: [],
};
