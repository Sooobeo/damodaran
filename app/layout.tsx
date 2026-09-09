import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '가치평가 공부방 — 나의 속도로, 깊이 있게',
  description: 'Aswath Damodaran의 자료로 배우는 나만의 한국어 가치평가 학습실',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ko"><body>{children}</body></html>;
}
