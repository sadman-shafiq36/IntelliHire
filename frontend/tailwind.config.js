/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // accent for primary actions
        brand: {
          50: '#eef4ff',
          100: '#d9e5ff',
          500: '#3b63d4',
          600: '#2f4fb0',
          700: '#26408f',
        },
      },
    },
  },
  plugins: [],
}
