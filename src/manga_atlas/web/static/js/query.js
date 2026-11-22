// Search-box syntax: only `#标签`, `-#排除` and `作者:`; everything else is title text.

const EXCLUDE = /^[-－][#＃]/;
const INCLUDE = /^[#＃]/;
const AUTHOR = /^作者[:：]/;
const SPACE = /\s/; // \s includes the ideographic space
const SPACES = /\s+/;

export function classify(token) {
  for (const [kind, re] of [['exclude', EXCLUDE], ['include', INCLUDE], ['author', AUTHOR]]) {
    const m = token.match(re);
    if (m) return { kind, value: token.slice(m[0].length) };
  }
  return { kind: 'text', value: token };
}

export function parseQuery(input) {
  const out = { include: [], exclude: [], authors: [], text: '' };
  const words = [];
  const tokens = input.split(SPACES).filter(Boolean);
  for (let i = 0; i < tokens.length; i += 1) {
    const { kind, value } = classify(tokens[i]);
    if (kind === 'text') {
      words.push(value);
      continue;
    }
    let v = value;
    if (!v && kind === 'author' && i + 1 < tokens.length) v = tokens[(i += 1)]; // "作者: 名字"
    if (!v) continue;
    out[kind === 'author' ? 'authors' : kind].push(v);
  }
  out.text = words.join(' ');
  return out;
}

// The whitespace-delimited token around the caret.
export function tokenAt(input, caret) {
  let start = caret;
  let end = caret;
  while (start > 0 && !SPACE.test(input[start - 1])) start -= 1;
  while (end < input.length && !SPACE.test(input[end])) end += 1;
  return { start, end, text: input.slice(start, end) };
}
