/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    // the default breakpoints scaled with the 75% root size, so layouts switch at the same visual width
    screens: {
      sm: '480px',
      md: '576px',
      lg: '768px',
      xl: '960px',
      '2xl': '1152px',
    },
    extend: {
      fontFamily: {
        sans: [
          '"Schibsted Grotesk Variable"',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          '"Segoe UI"',
          'sans-serif',
        ],
      },
      colors: {
        // neutrals lean slightly green so paper, rules and ink read as one family.
        // 500 and up pass 4.5:1 on white for body text
        slate: {
          50: '#f4f5f1',
          100: '#eaece6',
          200: '#d9dcd2',
          300: '#c2c6ba',
          400: '#8f958b',
          500: '#61685c',
          600: '#4b5248',
          700: '#383e37',
          800: '#252b27',
          900: '#17211f',
        },
        // verdigris, the colour of actions
        brand: {
          50: '#e8f3ef',
          100: '#cde6dd',
          200: '#a3d1c1',
          500: '#1a8a70',
          600: '#126e5a',
          700: '#0d5848',
          800: '#0a4538',
        },
        // the highlighter: only for "this is the current / the evidence"
        marker: {
          DEFAULT: '#eee35a',
          soft: '#f7f1a6',
        },
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
    },
  },
  plugins: [],
}
