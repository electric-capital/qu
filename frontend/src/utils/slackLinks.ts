/**
 * Helpers for building URLs that deep-link into a Slack thread.
 *
 * We use Slack's native `slack://` URI scheme so clicking the link hands off
 * directly to the installed Slack desktop app rather than opening the web
 * client at app.slack.com. Slack's official docs document
 * `slack://channel?team={TEAM_ID}&id={CHANNEL_ID}`; the `&message={TS}` query
 * parameter is additionally honoured by the native client to scroll to (and
 * open the thread on) that specific parent message. See
 * https://docs.slack.dev/interactivity/deep-linking/ for the documented
 * portion of the scheme.
 *
 * The `thread_ts` we persist is the parent-message timestamp, which is the
 * correct value for the `message` param.
 */

export function buildSlackThreadUrl(
  teamId: string,
  channelId: string,
  threadTs: string,
): string {
  const params = new URLSearchParams({
    team: teamId,
    id: channelId,
    message: threadTs,
  });
  return `slack://channel?${params.toString()}`;
}
