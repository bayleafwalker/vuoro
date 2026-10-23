from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.artifact_read import ArtifactRead
from ...types import Response


def _get_kwargs(
    request_id: str,
    material_revision: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/v3/requests/{request_id}/artifact/{material_revision}".format(
            request_id=quote(str(request_id), safe=""),
            material_revision=quote(str(material_revision), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> ArtifactRead | None:
    if response.status_code == 200:
        response_200 = ArtifactRead.from_dict(response.json())

        return response_200

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ArtifactRead]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    request_id: str,
    material_revision: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ArtifactRead]:
    """
    Args:
        request_id (str):
        material_revision (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactRead]
    """

    kwargs = _get_kwargs(
        request_id=request_id,
        material_revision=material_revision,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    request_id: str,
    material_revision: str,
    *,
    client: AuthenticatedClient | Client,
) -> ArtifactRead | None:
    """
    Args:
        request_id (str):
        material_revision (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactRead
    """

    return sync_detailed(
        request_id=request_id,
        material_revision=material_revision,
        client=client,
    ).parsed


async def asyncio_detailed(
    request_id: str,
    material_revision: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[ArtifactRead]:
    """
    Args:
        request_id (str):
        material_revision (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ArtifactRead]
    """

    kwargs = _get_kwargs(
        request_id=request_id,
        material_revision=material_revision,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    request_id: str,
    material_revision: str,
    *,
    client: AuthenticatedClient | Client,
) -> ArtifactRead | None:
    """
    Args:
        request_id (str):
        material_revision (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ArtifactRead
    """

    return (
        await asyncio_detailed(
            request_id=request_id,
            material_revision=material_revision,
            client=client,
        )
    ).parsed
