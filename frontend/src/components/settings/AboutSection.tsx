import { useEffect, useState } from 'react';
import { QuestLogo } from '../QuestLogo';
import { HeartBoltMorph } from '../HeartBoltMorph';
import { fetchVersion } from '../../api/client';
import type { VersionResponse } from '../../api/types';
import './AboutSection.css';

// Placeholder repository link -- the public repo will live at a different URL.
const GITHUB_URL = 'https://github.com/electric-capital/quest';
const LICENSE_URL = 'https://www.apache.org/licenses/LICENSE-2.0';

function formatReleaseDate(iso: string | null): string {
  if (!iso) return 'Unreleased';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
}

/**
 * Release metadata comes from the server's git checkout (nearest
 * `v<semver>` tag, see config/version.py) so it reflects what is actually
 * running rather than what was built into the bundle.
 */
export function AboutSection() {
  const [info, setInfo] = useState<VersionResponse | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    fetchVersion()
      .then((data) => { if (!cancelled) setInfo(data); })
      .catch(() => { if (!cancelled) setInfo(null); });
    return () => { cancelled = true; };
  }, []);

  const shortHash = info?.git_hash ? info.git_hash.slice(0, 7) : null;
  let versionLabel: string;
  if (info === undefined) {
    versionLabel = '…';
  } else if (info?.version) {
    versionLabel = info.version;
  } else if (shortHash) {
    versionLabel = `unreleased (${shortHash})`;
  } else {
    versionLabel = 'unknown';
  }
  const isPostReleaseBuild = !!info?.version && info.commits_since_tag > 0;

  return (
    <div className="settings-section about-section">
      <h3>About</h3>
      <div className="about-card">
        <QuestLogo className="about-logo" />
        <h2 className="about-name">Quest</h2>
        <dl className="about-meta">
          <div className="about-meta-row">
            <dt>Version</dt>
            <dd title={info?.git_hash ?? undefined}>{versionLabel}</dd>
          </div>
          <div className="about-meta-row">
            <dt>Released</dt>
            <dd>{info === undefined ? '…' : formatReleaseDate(info?.released ?? null)}</dd>
          </div>
          {isPostReleaseBuild && shortHash && (
            <div className="about-meta-row">
              <dt>Commit</dt>
              <dd>{shortHash}</dd>
            </div>
          )}
        </dl>
        {isPostReleaseBuild && (
          <p className="about-build-note">
            This instance is running {info!.commits_since_tag} commit{info!.commits_since_tag === 1 ? '' : 's'} past release {info!.tag}.
          </p>
        )}
        <p className="about-credit">
          Made with <HeartBoltMorph className="about-heart" size={22} /> by Electric Capital
        </p>
        <p className="about-license">
          Quest is open source, distributed under the{' '}
          <a href={LICENSE_URL} target="_blank" rel="noopener noreferrer">Apache License 2.0</a>.
          You are free to use, modify, and redistribute it under the terms of that license.
        </p>
        <a className="about-github-link" href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
          </svg>
          View on GitHub
        </a>
      </div>
    </div>
  );
}
