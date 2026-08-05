import React from 'react';
import QuestionFavoritesPanel from './QuestionFavoritesPanel';

export default function KnowledgePointFavoritesPanel({ onNavigate }) {
  return <QuestionFavoritesPanel collectionType="knowledge" onNavigate={onNavigate} />;
}