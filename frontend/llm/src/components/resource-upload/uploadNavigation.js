export const UPLOAD_TYPES = ['textbook', 'knowledge', 'question', 'syllabus'];

export const uploadIntent = (type) => ({
  page: 'personalization',
  params: { view: 'resources', uploadType: UPLOAD_TYPES.includes(type) ? type : '' },
});