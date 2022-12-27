package v1

import (
	"bytes"
	"encoding/json"
	"github.com/nwangfw/kubeml/ml/pkg/api"
	"github.com/pkg/errors"
	"io/ioutil"
	"net/http"
)

type (
	NetworkGetter interface {
		Networks() NetworkInterface
	}

	NetworkInterface interface {
		Train(req *api.TrainRequest) (string, error)
		Infer(req *api.InferRequest) ([]byte, error)
	}

	networks struct {
		controllerUrl string
		httpClient    *http.Client
	}
)

func newNetworks(c *V1) NetworkInterface {
	return &networks{
		controllerUrl: c.controllerUrl,
		httpClient:    c.httpClient,
	}
}

func (n *networks) Train(req *api.TrainRequest) (string, error) {
	url := n.controllerUrl + "/train"

	body, err := json.Marshal(req)
	if err != nil {
		return "", errors.Wrap(err, "could not send train job to the controller")
	}

	// send the request and get the task id
	// TODO this task id could be generated by the client
	resp, err := n.httpClient.Post(url, "application/json", bytes.NewBuffer(body))
	if err != nil {
		return "", errors.Wrap(err, "could not process train job")
	}

	defer resp.Body.Close()

	id, err := ioutil.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}

	return string(id), nil
}

func (n *networks) Infer(req *api.InferRequest) ([]byte, error) {
	url := n.controllerUrl + "/infer"

	// Create the request body
	body, err := json.Marshal(req)
	if err != nil {
		return nil, errors.Wrap(err, "could not send train request to scheduler")
	}

	// Send the request and return the id
	resp, err := n.httpClient.Post(url, "application/json", bytes.NewBuffer(body))
	if err != nil {
		return nil, errors.Wrap(err, "could not process inference job")
	}
	defer resp.Body.Close()

	body, err = ioutil.ReadAll(resp.Body)
	if err != nil {
		return nil, errors.Wrap(err, "could not read response body")
	}

	return body, nil
}
