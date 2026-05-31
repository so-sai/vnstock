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
        },
        stock: {
          up: '#00b050',
          down: '#ff0000',
          ref: '#ffc000',
          ceil: '#cc00ff',
          floor: '#00b0f0',
        }
      }
    },
  },
  plugins: [],
}

export default config
