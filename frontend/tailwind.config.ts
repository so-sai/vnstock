import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@tremor/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        japandi: {
          oat: '#ebe6d6',
          'warm-sand': '#ded5b9',
          'muted-clay': '#cec4a7',
          moss: '#707e57',
          earth: '#6b6445',
          rust: '#a66144',
        }
      }
    },
  },
  plugins: [],
}

export default config
