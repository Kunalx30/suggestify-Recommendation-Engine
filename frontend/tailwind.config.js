/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        netflix: '#E50914',
        dark: '#141414',
        card: '#1a1a1a',
      }
    }
  },
  plugins: [],
}
