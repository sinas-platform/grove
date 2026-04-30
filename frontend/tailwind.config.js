/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        forest: {
          50: '#f3f8f3',
          100: '#dfeddc',
          500: '#3f6e3a',
          600: '#345a30',
          700: '#284627',
          900: '#152618',
        },
      },
    },
  },
  plugins: [],
};
