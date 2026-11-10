/** Parameter edges enter at the card boundary before reaching their measured field socket. */
export function connectionPath(sx: number, sy: number, tx: number, ty: number, boundaryX?: number): string {
  const entryX = boundaryX !== undefined && tx > boundaryX ? boundaryX : tx
  const bend = Math.max(48, Math.abs(entryX - sx) * .45)
  const curve = `M ${sx} ${sy} C ${sx + bend} ${sy}, ${entryX - bend} ${ty}, ${entryX} ${ty}`
  return entryX === tx ? curve : `${curve} L ${tx} ${ty}`
}
/** This is exactly the last segment of connectionPath with the same boundary. */
export function connectionEntryPath(_sx: number, _sy: number, tx: number, ty: number, boundaryX: number): string {
  return tx > boundaryX ? `M ${boundaryX} ${ty} L ${tx} ${ty}` : ''
}
