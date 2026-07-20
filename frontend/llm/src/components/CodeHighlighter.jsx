import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

export default function CodeHighlighter({ language, children, ...props }) {
  return (
    <SyntaxHighlighter
      style={vscDarkPlus}
      language={language}
      PreTag="div"
      customStyle={{ margin: 0, padding: '1.25rem', background: 'transparent', fontSize: '0.875rem', lineHeight: '1.6' }}
      {...props}
    >
      {children}
    </SyntaxHighlighter>
  );
}
