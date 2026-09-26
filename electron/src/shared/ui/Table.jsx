import React from 'react';
import { Search, X } from 'lucide-react';
import { Input, Button } from './index.js';

/**
 * Table.Toolbar — search + filters row above a virtualised table.
 *
 * Use alone when you just want the styled search/filter chrome without the
 * full <Table> wrapper; use inside <Table> for a cohesive chrome treatment.
 */
function Toolbar({
  search,
  onSearch,
  searchPlaceholder = 'Search…',
  meta, // right-aligned summary (e.g. "42/42 · 3 sel")
  children, // extra filter controls between search and meta
  className = '',
  ...rest
}) {
  return (
    <div className={`ui-table-toolbar ${className}`} {...rest}>
      {onSearch != null && (
        <div className="ui-table-toolbar__search">
          <Search size={10} className="ui-table-toolbar__search-icon" />
          <Input
            size="sm"
            placeholder={searchPlaceholder}
            value={search || ''}
            onChange={(e) => onSearch(e.target.value)}
            className="ui-table-toolbar__search-input"
          />
          {search && (
            <Button
              variant="ghost"
              iconSize="sm"
              onClick={() => onSearch('')}
              className="ui-table-toolbar__search-clear"
              title="Clear"
            >
              <X size={10} />
            </Button>
          )}
        </div>
      )}
      {children}
      {meta != null && <span className="ui-table-toolbar__meta">{meta}</span>}
    </div>
  );
}

/**
 * Table.Header — sticky column header row.
 *
 * Columns: array of { key, label, width?, flex?, align? }
 */
function Header({ columns = [], leading, trailing, className = '' }) {
  return (
    <div className={`ui-table-header ${className}`}>
      {leading}
      {columns.map((c) => (
        <span
          key={c.key}
          className={`ui-table-header__cell ui-table-header__cell--align-${c.align || 'left'}`}
          style={c.width ? { width: c.width, flex: 'none' } : { flex: c.flex ?? 1 }}
          title={c.title}
        >
          {c.label}
        </span>
      ))}
      {trailing}
    </div>
  );
}

/**
 * Table — thin wrapper that provides a toolbar slot + header slot + children body.
 * Caller controls virtualisation (react-window).
 *
 *   <Table>
 *     <Table.Toolbar search={q} onSearch={setQ} meta={`${n}/${t}`}>
 *       <Select>…</Select>
 *     </Table.Toolbar>
 *     <Table.Header columns={cols} leading={<checkbox />} />
 *     <div className="...virtualised list...">
 *       <List rowComponent={Row} ... />
 *     </div>
 *   </Table>
 */
export default function Table({ className = '', children, ...rest }) {
  return (
    <div className={`ui-table ${className}`} {...rest}>
      {children}
    </div>
  );
}

Table.Toolbar = Toolbar;
Table.Header = Header;
