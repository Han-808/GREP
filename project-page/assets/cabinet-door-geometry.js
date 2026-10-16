/**
 * Horizontal-section collision for a door rotating about its vertical hinge.
 * Positive opening angles swing the door from +x toward +z. Distances are in m.
 * Each door part is an axis-aligned rectangle in the closed door's coordinates.
 * The obstacle is a circular section of the ottoman at its widest intersecting y.
 */
export function doorClearance(angle, { hinge, obstacle, parts }) {
  const dx = obstacle.x - hinge.x;
  const dz = obstacle.z - hinge.z;
  const c = Math.cos(angle), s = Math.sin(angle);
  const x = c * dx + s * dz;
  const z = -s * dx + c * dz;
  let closest = { clearance: Infinity, x: 0, z: 0 };
  for (const part of parts) {
    if (part.radius === null) continue;
    const px = Math.max(part.minX, Math.min(part.maxX, x));
    const pz = Math.max(part.minZ, Math.min(part.maxZ, z));
    const clearance = Math.hypot(x - px, z - pz) - (part.radius ?? obstacle.radius);
    if (clearance < closest.clearance) {
      closest = { clearance, x: hinge.x + c * px - s * pz, z: hinge.z + s * px + c * pz };
    }
  }
  return closest;
}

/** First contact along the swing, not just collision at the requested endpoint. */
export function doorSwingLimit(spec, maxAngle = Math.PI / 2) {
  const safetyGap = 0.001;
  if (doorClearance(0, spec).clearance <= safetyGap) return 0;
  const intervals = Math.ceil(maxAngle / (Math.PI / 720));
  let previous = 0;
  for (let i = 1; i <= intervals; i++) {
    const next = maxAngle * i / intervals;
    if (doorClearance(next, spec).clearance <= safetyGap) {
      let low = previous, high = next;
      for (let j = 0; j < 36; j++) {
        const mid = (low + high) / 2;
        if (doorClearance(mid, spec).clearance > safetyGap) low = mid;
        else high = mid;
      }
      return low;
    }
    previous = next;
  }
  return maxAngle;
}
