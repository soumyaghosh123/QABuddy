import { useState } from 'react';
import { guessSource, sourceLabel, sourceShort } from '../sourceMeta';

interface Props {
  questions: string[];
  onPick: (question: string) => void;
}

// Geometry in the SVG's own units; the stage scales it with a fixed aspect ratio.
const W = 880;
const H = 500;
const CX = W / 2;
const CY = H / 2;
const RX = 320;
const RY = 195;
const RINGS = [0.24, 0.44, 0.64, 0.84];
const FIRST_ANGLE = 240; // top-left, then clockwise

type Point = [number, number];

const point = (deg: number, scale = 1): Point => {
  const t = (deg * Math.PI) / 180;
  return [CX + RX * scale * Math.cos(t), CY + RY * scale * Math.sin(t)];
};
const fmt = ([x, y]: Point) => `${x.toFixed(1)} ${y.toFixed(1)}`;

// A ring sags a little toward the hub between radials, like silk under tension.
function ringPath(scale: number): string {
  let d = `M${fmt(point(0, scale))}`;
  for (let i = 0; i < 12; i++) {
    const [ax, ay] = point(i * 30, scale);
    const [bx, by] = point((i + 1) * 30, scale);
    const sag: Point = [CX + ((ax + bx) / 2 - CX) * 0.9, CY + ((ay + by) / 2 - CY) * 0.9];
    d += ` Q${fmt(sag)} ${fmt([bx, by])}`;
  }
  return `${d} Z`;
}

const SILK = [
  ...Array.from({ length: 12 }, (_, i) => `M${CX} ${CY} L${fmt(point(i * 30, i % 2 ? 0.9 : 1))}`),
  ...RINGS.map(ringPath),
].join(' ');

const pct = (value: number, of: number) => `${((value / of) * 100).toFixed(2)}%`;

export function QuestionWeb({ questions, onPick }: Props) {
  const [hot, setHot] = useState<number | null>(null);
  const nodes = questions.map((q, i) => ({
    q,
    at: point(FIRST_ANGLE + (i * 360) / questions.length),
    source: guessSource(q),
  }));

  return (
    <div className="web">
      <div className="web__stage">
        <svg className="web__silk" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
          <path className="web__rings" d={SILK} />
          {nodes.map((n, i) => (
            <line
              key={n.q}
              className={`web__spoke${hot === i ? ' web__spoke--hot' : ''}`}
              x1={CX}
              y1={CY}
              x2={n.at[0]}
              y2={n.at[1]}
            />
          ))}
        </svg>
        <div className="web__hub" aria-hidden="true">
          <span className="web__hub-icon">🧪</span>
          <span className="web__hub-label">Try asking</span>
        </div>
        <ul className="web__nodes">
          {nodes.map((n, i) => (
            <li key={n.q} className="web__slot" style={{ left: pct(n.at[0], W), top: pct(n.at[1], H) }}>
              <button
                type="button"
                className="web__node"
                onClick={() => onPick(n.q)}
                onMouseEnter={() => setHot(i)}
                onMouseLeave={() => setHot(null)}
                onFocus={() => setHot(i)}
                onBlur={() => setHot(null)}
              >
                {n.source && (
                  <span className={`tag tag--${n.source}`} title={sourceLabel(n.source)}>
                    {sourceShort(n.source)}
                  </span>
                )}
                <span className="web__q">{n.q}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
