import json
import urllib.request
import urllib.error


class ErfasstApiError(Exception):
    """Raised for HTTP errors or GraphQL error responses from the 123erfasst API."""


class ErfasstClient:
    """Thin GraphQL client for the 123erfasst API.

    Authentication is HTTP Basic with a pre-encoded token string.
    All write operations use upsert semantics with referBy=FID for idempotency.
    """

    _GQL_UPSERT_PROJECT = """
        mutation UpsertProject($input: [InputProject]!, $referBy: ReferBy) {
          upsertProject(input: $input, referBy: $referBy) {
            ... on UpsertProjectResultSuccess {
              entities { ident fid name status }
            }
            ... on UpsertProjectValidationError {
              errors { entity { ident name } }
            }
            ... on UpsertProjectPermissionError {
              errors { entity { ident name } }
            }
            ... on UpsertProjectNotFoundError {
              errors { entity { ident name } }
            }
            ... on UpsertProjectStateError {
              errors { entity { ident name } }
            }
          }
        }
    """

    _GQL_GET_TIMES = """
        query GetTimes($filter: TimeCollectionFilter, $skip: Int, $take: Int) {
          times(filter: $filter, skip: $skip, take: $take, orderBy: { date: ASC }) {
            nodes {
              ident
              fid
              date
              timeStart
              timeEnd
              text
              isLocked
              isProved
              person { ident fid firstname lastname }
              project { ident fid name }
              activity { ident fid name }
            }
            totalCount
          }
        }
    """

    _GQL_GET_PERSONS = """
        query GetPersons($skip: Int, $take: Int) {
          persons(skip: $skip, take: $take) {
            nodes {
              ident
              fid
              firstname
              lastname
              contact { email }
            }
            totalCount
          }
        }
    """

    _GQL_GET_ACTIVITIES = """
        query GetActivities($skip: Int, $take: Int) {
          activities(skip: $skip, take: $take) {
            nodes {
              ident
              fid
              name
            }
            totalCount
          }
        }
    """

    _GQL_GET_PROJECTS = """
        query GetProjects($skip: Int, $take: Int) {
          projects(skip: $skip, take: $take) {
            nodes {
              ident
              fid
              id
              name
              status
              startDate
            }
            totalCount
          }
        }
    """

    _GQL_UPSERT_PLANNING = """
        mutation UpsertPlanning($input: [InputPlanning]!, $referBy: ReferBy) {
          upsertPlanning(input: $input, referBy: $referBy) {
            ... on UpsertPlanningResultSuccess {
              entities { ident fid dateStart dateEnd }
            }
            ... on UpsertPlanningValidationError {
              errors { entity { ident } }
            }
            ... on UpsertPlanningPermissionError {
              errors { entity { ident } }
            }
            ... on UpsertPlanningNotFoundError {
              errors { entity { ident } }
            }
            ... on UpsertPlanningStateError {
              errors { entity { ident } }
            }
          }
        }
    """

    def __init__(self, api_url: str, api_token: str):
        self._api_url = api_url.rstrip('/')
        self._api_token = api_token

    @classmethod
    def from_env(cls, env):
        """Build client from Odoo ir.config_parameter. Raises UserError if config is missing."""
        from odoo.exceptions import UserError

        ICP = env['ir.config_parameter'].sudo()
        url = ICP.get_param('erfasst_connector.api_url', '').strip()
        token = ICP.get_param('erfasst_connector.api_token', '').strip()

        if not url:
            raise UserError(
                'Die 123erfasst API-URL ist nicht konfiguriert.\n'
                'Bitte unter Einstellungen → 123erfasst Connector eintragen.'
            )
        if not token:
            raise UserError(
                'Der 123erfasst API-Token ist nicht konfiguriert.\n'
                'Bitte unter Einstellungen → 123erfasst Connector eintragen.'
            )
        return cls(url, token)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(self, query: str, variables: dict | None = None) -> dict:
        """Execute one GraphQL request. Returns response['data']. Raises ErfasstApiError."""
        payload = json.dumps({
            'query': query,
            'variables': variables or {},
        }).encode('utf-8')

        req = urllib.request.Request(
            self._api_url,
            data=payload,
            headers={
                'Authorization': f'Basic {self._api_token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            method='POST',
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            raise ErfasstApiError(f'HTTP {exc.code}: {exc.reason}') from exc
        except urllib.error.URLError as exc:
            raise ErfasstApiError(f'Verbindungsfehler: {exc.reason}') from exc

        errors = body.get('errors')
        if errors:
            messages = '; '.join(e.get('message', str(e)) for e in errors)
            raise ErfasstApiError(f'GraphQL-Fehler: {messages}')

        return body.get('data', {})

    def _paginate(self, query: str, variables: dict, root_key: str, page_size: int = 200) -> list:
        """Auto-paginate a collection query. Returns all nodes as a flat list."""
        results = []
        skip = 0
        while True:
            page_vars = {**variables, 'skip': skip, 'take': page_size}
            data = self._execute(query, page_vars)
            collection = data.get(root_key) or {}
            page = collection.get('nodes') or []
            results.extend(page)
            if len(page) < page_size:
                break
            skip += page_size
        return results

    def _raise_mutation_errors(self, result: dict, operation: str):
        """Inspect a union mutation result and raise ErfasstApiError on non-success types."""
        if result is None:
            raise ErfasstApiError(f'{operation}: leere Antwort vom Server')

        if 'entities' in result:
            return  # UpsertXResultSuccess

        errors = result.get('errors') or []
        messages = []
        for e in errors:
            entity = e.get('entity') or {}
            label = entity.get('name') or entity.get('ident') or '?'
            messages.append(label)
        raise ErfasstApiError(f'{operation} fehlgeschlagen: {"; ".join(messages) or "unbekannter Fehler"}')

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def upsert_project(self, inputs: list) -> dict:
        """Push projects to 123erfasst. Returns the first created/updated entity dict."""
        data = self._execute(self._GQL_UPSERT_PROJECT, {
            'input': inputs,
            'referBy': 'FID',
        })
        result = data.get('upsertProject') or {}
        self._raise_mutation_errors(result, 'upsertProject')
        entities = result.get('entities') or []
        return entities[0] if entities else {}

    def get_times(self, filter_vars: dict) -> list:
        """Fetch all StaffTime records matching filter_vars. Auto-paginates."""
        return self._paginate(
            self._GQL_GET_TIMES,
            {'filter': filter_vars},
            root_key='times',
        )

    def get_persons(self) -> list:
        """Fetch all Person records from 123erfasst. Auto-paginates."""
        return self._paginate(self._GQL_GET_PERSONS, {}, root_key='persons')

    def get_activities(self) -> list:
        """Fetch all Activity records from 123erfasst. Auto-paginates."""
        return self._paginate(self._GQL_GET_ACTIVITIES, {}, root_key='activities')

    def get_projects(self) -> list:
        """Fetch all Project records from 123erfasst. Auto-paginates."""
        return self._paginate(self._GQL_GET_PROJECTS, {}, root_key='projects')

    def test_connection(self) -> int:
        """Minimal API call to verify credentials. Returns total person count."""
        data = self._execute('query { persons(take: 1) { totalCount } }', {})
        return (data.get('persons') or {}).get('totalCount', 0)

    def upsert_planning(self, inputs: list) -> list:
        """Push planning entries to 123erfasst. Returns list of created/updated entities."""
        data = self._execute(self._GQL_UPSERT_PLANNING, {
            'input': inputs,
            'referBy': 'FID',
        })
        result = data.get('upsertPlanning') or {}
        self._raise_mutation_errors(result, 'upsertPlanning')
        return result.get('entities') or []
