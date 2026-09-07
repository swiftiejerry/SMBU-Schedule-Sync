/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          50: '#F7F7F8',
          100: '#EEEFF1',
          200: '#D9DBDF',
          300: '#B8BCC4',
          400: '#8E949F',
          500: '#6B7280',
          600: '#4B525C',
          700: '#353A43',
          800: '#22262D',
          900: '#14171C',
          950: '#0B0D10',
        },
        accent: {
          50: '#EFF4FF',
          100: '#DBE6FE',
          200: '#BFD1FE',
          300: '#93B4FD',
          400: '#608EFA',
          500: '#3B6DF6',
          600: '#254FEB',
          700: '#1D3DD8',
          800: '#1E33AF',
          900: '#1E318A',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'PingFang SC',
          'Hiragino Sans GB',
          'Microsoft YaHei',
          'Noto Sans SC',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        xl: '0.75rem',
        '2xl': '1rem',
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.22, 1, 0.36, 1)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both',
        shimmer: 'shimmer 1.8s linear infinite',
      },
    },
  },
  plugins: [],
}
