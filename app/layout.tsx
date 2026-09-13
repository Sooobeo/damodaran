import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '달모다란 (MoonModaran)',
  description: 'Aswath Damodaran의 자료로 배우는 나만의 한국어 가치평가 학습실',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ko"><body>{children}</body></html>;
}
