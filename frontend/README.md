# Agent Memory System - Frontend

React + TypeScript frontend for the Agent Memory System.

## Setup

### Prerequisites
- Node.js 22+
- npm 10+

### Installation

1. Install dependencies:
```bash
npm install
```

## Running the Application

### Development mode:
```bash
npm run dev
```

The application will start at http://localhost:5173

### Production build:
```bash
npm run build
```

The built files will be in the `dist/` directory.

### Preview production build:
```bash
npm run preview
```

## Project Structure

```
frontend/
├── src/
│   ├── components/   # Reusable UI components
│   ├── pages/        # Page components
│   ├── services/     # API services and utilities
│   ├── stores/       # Zustand state management
│   ├── App.tsx      # Main application component
│   └── main.tsx     # Application entry point
├── public/           # Static assets
├── index.html        # HTML entry point
├── vite.config.ts   # Vite configuration
└── package.json     # npm configuration
```

## Technologies Used

- **React 19**: UI framework
- **TypeScript**: Type-safe JavaScript
- **Vite**: Build tool and dev server
- **Ant Design**: UI component library
- **Zustand**: State management
- **React Router**: Client-side routing
- **Axios**: HTTP client

## Proxy Configuration

The development server is configured to proxy API requests to the backend:

- API requests to `/api/*` are proxied to `http://localhost:8000`

This configuration is in `vite.config.ts`.

## Available Scripts

- `npm run dev` - Start development server
- `npm run build` - Build for production
- `npm run preview` - Preview production build locally
- `npm run lint` - Run ESLint

## Features

- Modern React with hooks
- TypeScript for type safety
- Ant Design components
- Client-side routing
- State management with Zustand
- API integration with Axios
- Hot Module Replacement (HMR) with Vite
