import { Suspense } from 'react';
import StudyRoom from '@/components/study-room';

export default function Page() {
  return <Suspense fallback={<div className="initial-loading">공부방을 열고 있습니다…</div>}><StudyRoom /></Suspense>;
}
